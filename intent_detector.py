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
Return only valid JSON with this schema:
{"is_os_operation": true|false, "confidence": 0.0-1.0}

Set is_os_operation=true only when the user is clearly asking to interact with files, folders, paths, directories, or OS file management.
Use higher confidence for explicit filesystem requests and lower confidence for ambiguous phrasing.
Return confidence below 0.7 when unsure.

Examples:
- "list all files on desktop" -> {"is_os_operation": true, "confidence": 0.98}
- "show my documents" -> {"is_os_operation": true, "confidence": 0.85}
- "read /tmp/test.txt" -> {"is_os_operation": true, "confidence": 0.99}
- "create a folder named hello" -> {"is_os_operation": true, "confidence": 0.96}
- "what is the weather" -> {"is_os_operation": false, "confidence": 0.01}
- "hello how are you" -> {"is_os_operation": false, "confidence": 0.01}
- "create file space for storage" -> {"is_os_operation": false, "confidence": 0.15}
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
    r"\b(?:create|make|new|add|write|generate|save|store)\b",
    r"\b(?:delete|remove|trash|rm|unlink)\b",
    r"\b(?:read|open|view|show|display|list|ls|cat)\b",
    r"\b(?:rename|move|copy|modify|update|change)\b",
    r"(?:~|/|\\\\|\.\/|[A-Za-z]:)",
    r"\b(?:file|folder|directory|document|documents|pdf|png|jpg|jpeg|text|desktop|downloads|path|paths)\b",
)
_NEGATIVE_HEURISTIC_PATTERNS = (
    r"\bfile\s+space\b",
    r"\bstorage\s+space\b",
)
_INTENT_CONFIDENCE_THRESHOLD = 0.7


def _normalize_input(user_input: str) -> str:
    return " ".join(str(user_input or "").strip().split())


def _cache_key(user_input: str, include_time_bucket: bool = False) -> str:
    normalized = _normalize_input(user_input).lower()
    if include_time_bucket:
        bucket = int(time.time() // 60)
        normalized = f"{normalized}|{bucket}"
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()


def _heuristic_intent_result(user_input: str) -> Dict[str, object]:
    normalized = _normalize_input(user_input).lower()
    if any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in _NEGATIVE_HEURISTIC_PATTERNS):
        return {"is_os_operation": False, "confidence": 0.5, "source": "heuristic"}

    matches = {
        "action": bool(re.search(_HEURISTIC_OS_PATTERNS[0], normalized, flags=re.IGNORECASE))
        or bool(re.search(_HEURISTIC_OS_PATTERNS[1], normalized, flags=re.IGNORECASE))
        or bool(re.search(_HEURISTIC_OS_PATTERNS[2], normalized, flags=re.IGNORECASE))
        or bool(re.search(_HEURISTIC_OS_PATTERNS[3], normalized, flags=re.IGNORECASE)),
        "path": bool(re.search(_HEURISTIC_OS_PATTERNS[4], normalized, flags=re.IGNORECASE)),
        "file_keyword": bool(re.search(_HEURISTIC_OS_PATTERNS[5], normalized, flags=re.IGNORECASE)),
    }

    is_os_operation = bool(matches["path"] or matches["file_keyword"] or (matches["action"] and matches["file_keyword"]))
    return {
        "is_os_operation": is_os_operation,
        "confidence": 0.5,
        "source": "heuristic",
        "heuristic_matches": matches,
    }


def _parse_intent_response(raw_response: str) -> Dict[str, object]:
    parsed = json.loads(_extract_json_object(raw_response))
    if not isinstance(parsed, dict):
        raise ValueError("Intent classifier returned non-object JSON.")

    confidence = float(parsed.get("confidence", 0.0))
    confidence = max(0.0, min(1.0, confidence))
    return {
        "is_os_operation": bool(parsed.get("is_os_operation", False)) and confidence >= _INTENT_CONFIDENCE_THRESHOLD,
        "raw_is_os_operation": bool(parsed.get("is_os_operation", False)),
        "confidence": confidence,
        "source": "ollama",
    }


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

    key = _cache_key(normalized, include_time_bucket=True)
    cached = _INTENT_CACHE.get(key)
    if cached:
        result, _ = cached
        return {
            **result,
            "from_cache": True,
            "source": result.get("source", "cache"),
            "response_time_ms": (time.time() - start) * 1000.0,
        }

    try:
        response = ollama_handler.query_ollama(
            user_input=f"Classify this request: {normalized}",
            conversation_history=[],
            system_prompt=_INTENT_SYSTEM_PROMPT,
            timeout_override=timeout_seconds,
        )
        parsed = _parse_intent_response(response)
        result = {
            **parsed,
            "from_cache": False,
            "threshold": _INTENT_CONFIDENCE_THRESHOLD,
            "response_time_ms": (time.time() - start) * 1000.0,
            "error": None,
        }
    except Exception as exc:
        heuristic = _heuristic_intent_result(normalized)
        result = {
            "is_os_operation": bool(heuristic.get("is_os_operation", False))
            and float(heuristic.get("confidence", 0.0)) >= _INTENT_CONFIDENCE_THRESHOLD,
            "raw_is_os_operation": bool(heuristic.get("is_os_operation", False)),
            "confidence": float(heuristic.get("confidence", 0.5)),
            "from_cache": False,
            "source": heuristic.get("source", "heuristic"),
            "threshold": _INTENT_CONFIDENCE_THRESHOLD,
            "response_time_ms": (time.time() - start) * 1000.0,
            "error": str(exc),
        }
        if "heuristic_matches" in heuristic:
            result["heuristic_matches"] = heuristic["heuristic_matches"]

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
