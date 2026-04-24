import json
import os
import threading
import time
import uuid
from typing import Dict, List

try:
    from PIL import Image
except Exception:
    Image = None


_BASE_DIR = os.path.dirname(__file__)
_HISTORY: List[Dict[str, str]] = []
_IS_INITIALIZED = False
_ENV_LOADED = False
_PENDING_FILE_OPERATION: Dict[str, str] = {}
_PENDING_OPERATIONS: Dict[str, Dict[str, object]] = {}
_OPERATIONS_LOCK = threading.Lock()


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


def set_pending_file_operation(operation: Dict[str, str]) -> None:
    global _PENDING_FILE_OPERATION
    _initialize()
    if not isinstance(operation, dict):
        _PENDING_FILE_OPERATION = {}
        return
    _PENDING_FILE_OPERATION = dict(operation)


def get_pending_file_operation() -> Dict[str, str]:
    _initialize()
    return dict(_PENDING_FILE_OPERATION)


def has_pending_file_operation() -> bool:
    _initialize()
    return bool(_PENDING_FILE_OPERATION)


def clear_pending_file_operation() -> None:
    global _PENDING_FILE_OPERATION
    _initialize()
    _PENDING_FILE_OPERATION = {}


def create_pending_operation(operation_type: str, **metadata) -> str:
    _initialize()
    operation_id = uuid.uuid4().hex
    now = time.time()
    record = {
        "id": operation_id,
        "type": str(operation_type or "unknown"),
        "status": "pending",
        "created_at": now,
        "updated_at": now,
        "result": metadata.pop("result", None),
        "error": metadata.pop("error", None),
    }
    if metadata:
        record.update(metadata)
    with _OPERATIONS_LOCK:
        _PENDING_OPERATIONS[operation_id] = record
    return operation_id


def update_pending_operation(
    operation_id: str,
    *,
    status: str = None,
    result=None,
    error: str = None,
    **metadata,
) -> Dict[str, object]:
    _initialize()
    with _OPERATIONS_LOCK:
        record = dict(_PENDING_OPERATIONS.get(operation_id, {}))
        if not record:
            record = {
                "id": operation_id,
                "type": str(metadata.pop("type", "unknown")),
                "created_at": time.time(),
            }
        if status is not None:
            record["status"] = str(status)
        if result is not None:
            record["result"] = result
        if error is not None:
            record["error"] = str(error)
        if metadata:
            record.update(metadata)
        record["updated_at"] = time.time()
        _PENDING_OPERATIONS[operation_id] = record
        return dict(record)


def get_pending_operation(operation_id: str) -> Dict[str, object]:
    _initialize()
    with _OPERATIONS_LOCK:
        return dict(_PENDING_OPERATIONS.get(operation_id, {}))


def get_pending_operations() -> Dict[str, Dict[str, object]]:
    _initialize()
    with _OPERATIONS_LOCK:
        return {key: dict(value) for key, value in _PENDING_OPERATIONS.items()}


def wait_for_pending_operation(
    operation_id: str,
    timeout_seconds: float = 2.0,
    poll_interval: float = 0.1,
) -> Dict[str, object]:
    _initialize()
    deadline = time.time() + max(0.0, float(timeout_seconds))
    while time.time() <= deadline:
        record = get_pending_operation(operation_id)
        if not record or record.get("status") in {"success", "failed"}:
            return record
        time.sleep(max(0.01, float(poll_interval)))
    return get_pending_operation(operation_id)


def clear_pending_operation(operation_id: str) -> None:
    _initialize()
    with _OPERATIONS_LOCK:
        _PENDING_OPERATIONS.pop(operation_id, None)


def clear_completed_operations(max_age_seconds: float = 300.0) -> None:
    _initialize()
    cutoff = time.time() - max(0.0, float(max_age_seconds))
    with _OPERATIONS_LOCK:
        expired = [
            op_id
            for op_id, record in _PENDING_OPERATIONS.items()
            if record.get("status") in {"success", "failed"} and float(record.get("updated_at", 0.0)) <= cutoff
        ]
        for op_id in expired:
            _PENDING_OPERATIONS.pop(op_id, None)


def validate_generated_image(image_path: str) -> Dict[str, object]:
    _initialize()
    path = _resolve_path(str(image_path or ""))
    result = {
        "path": path,
        "exists": False,
        "is_valid": False,
        "size": 0,
        "error": None,
    }

    if not image_path:
        result["error"] = "No image path provided."
        return result

    if not os.path.isfile(path):
        result["error"] = f"Image not found at {path}"
        return result

    size = os.path.getsize(path)
    result["exists"] = True
    result["size"] = size
    if size <= 0:
        result["error"] = "Image file is empty."
        return result

    try:
        with open(path, "rb") as image_file:
            header = image_file.read(4)
        if not header.startswith(b"\xff\xd8"):
            result["error"] = "Generated image is not a valid JPEG file."
            return result
        if Image is not None:
            with Image.open(path) as image:
                image.verify()
    except OSError as exc:
        result["error"] = str(exc)
        return result
    except Exception as exc:
        result["error"] = f"Image verification failed: {exc}"
        return result

    result["is_valid"] = True
    return result


def reset_history() -> None:
    _initialize()
    _HISTORY.clear()
    clear_pending_file_operation()
    with _OPERATIONS_LOCK:
        _PENDING_OPERATIONS.clear()
    _save_history_if_enabled()
